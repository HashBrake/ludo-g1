"""Train the Diffusion Policy, or the ACT baseline, of CLAUDE.md 5.7 on recorded sessions.

```bash
.venv/bin/python -m policy.train --sessions data/raw/20260912T090000 --steps 200000
.venv/bin/python -m policy.train --sessions data/raw/20260912T090000 --policy act   # the baseline
.venv/bin/python -m policy.train --sessions data/raw/mock_smoke --smoke      # 30 steps, batch 4, CPU
.venv/bin/python -m policy.train --sessions ... --config-block diffusion_small   # the D-019 fallback
.venv/bin/python -m policy.train --sessions ... --resume data/checkpoints/<run>/checkpoint.pt
.venv/bin/python -m policy.train --sessions ... --checkpoint-every 1000 --keep-last 2   # T-036
```

Real training happens on Greennode (5.8, Q-001); this is the entry point ``cloud/greennode.sh``
launches there and the one that runs on the laptop for a smoke test. It is a plain torch loop over
:class:`policy.dataset.LudoDataset` with the model's own loss -- no lerobot trainer, no hub.

``--policy act`` is the whole difference between the two models of 5.7: the same sessions, the same
:class:`~policy.dataset.LudoDataset` (at ``act.chunk`` and ``act.obs_history`` instead of the diffusion
block's), the same split, the same statistics, the same loop, the same run directory. CLAUDE.md 5.7
requires the baseline to be trained on every dataset the primary is trained on, so training it must
cost one flag and must not be able to change anything else. Defaults (steps, batch, lr, weight decay,
seed, and every schedule value below) come from the chosen policy's block in ``config/training.yaml``.

What the loop does besides stepping the optimiser (T-035)
---------------------------------------------------------
* **EMA.** :class:`EMA` keeps an exponential moving average of the parameters at
  ``<policy>.ema_decay`` beside the live weights, updated after every optimiser step. The average is
  what ``policy/export.py`` puts in a bundle by default (``--raw`` overrides), because the averaged
  weights are the ones a diffusion policy is evaluated with upstream.
* **Warmup then cosine.** The learning rate is ``learning_rate x`` :func:`lr_multiplier`: a linear
  ramp over ``warmup_steps`` steps, then a cosine decay to ``lr_min_ratio`` at the last step. The
  warmup is clamped to a tenth of a short run, so a 30-step smoke run still trains.
* **A validation split by cell pair.** ``--val-fraction`` holds out that fraction of the sessions'
  cell pairs through :func:`policy.dataset.split_cell_pairs` -- never a fraction of the frames, which
  would put the same pair on both sides (``dataset.split_by: cell_pair``). ROLL episodes address no
  pair, so they stay in training (their ``(None, None)`` pair is kept explicitly). The validation
  loss is measured every ``val_every`` steps over ``val_batches`` fixed batches and written as the
  ``val_loss`` column of ``loss.csv``.
* **Resume.** ``--stop-after N`` runs N steps of the run and checkpoints; ``--resume checkpoint.pt``
  continues it, restoring the weights, the optimiser, the EMA, the step counter, the loss history and
  the torch RNG state, with the batch stream positioned by step number (:class:`_StepSampler`). The
  schedule is computed against ``--steps``, the horizon of the whole run, so a run taken in slices is
  the same run: 10 steps + resume + 10 steps of a 20-step run **is** the 20-step run, and
  ``tests/test_train.py`` pins the two loss sequences and the two sets of weights against each other.

Surviving a crash (T-036)
-------------------------
A real run is hours of a rented GPU (5.8), so it is checkpointed as it goes, not only at the end:

* **Periodic.** ``--checkpoint-every N`` (``compute.checkpoint_every``) writes the checkpoint every N
  steps. Every write goes to ``checkpoint.pt.tmp`` and is then renamed over ``checkpoint.pt``
  (:func:`atomic_save`), so a crash *during* a write leaves the previous checkpoint whole rather than
  a truncated file. ``--resume`` on it continues the run exactly, by the machinery above.
* **Pruning.** Each periodic write also leaves ``checkpoint_step<N>.pt``, a hard link to the same
  file (a 4.7 GB checkpoint is not written twice and two names do not cost two inodes);
  ``--keep-last K`` keeps the K newest and deletes the rest. Nothing else in the run directory is
  ever pruned -- not ``checkpoint.pt`` and not an exported bundle.
* **The disk guard.** Before the first step the run compares the free space under its directory with
  ``compute.disk_guard_factor`` x the estimated checkpoint size (:func:`checkpoint_bytes`:
  parameters x 4 bytes x 4, printed either way) and refuses to start if it is short, naming
  ``agents/QUESTIONS.md`` Q-002. ``--no-disk-guard`` overrides it with a logged warning; it is a
  convenience against a full laptop disk, not a safety rule.

Every run writes ``data/checkpoints/<run>/``:

==========================  ======================================================================
``run.json``                args, git commit, the six config hashes, the dataset manifest hash, the
                            per-session episode and frame counts, the held-out pairs, the parameter
                            count and checkpoint size estimate, the final loss and the wall time
``loss.csv``                ``step,loss,val_loss,lr,elapsed_s`` for every step: the loss curves
                            CLAUDE.md section 8 audits (``val_loss`` is empty on a step with none)
``checkpoint.pt``           ``{"policy", "spec", "state_dict", "ema_state_dict", "ema_step",
                            "optimizer", "step", "rng", "losses", "run"}``; ``policy/export.py``
                            turns it into a bundle
``checkpoint_step<N>.pt``   the last ``--keep-last`` periodic copies of the same thing; ``run`` in
                            one of them is the run record so far, with ``complete: false``
==========================  ======================================================================

The dataset manifest hash is the sha256 of each session's ``meta/info.json`` and
``episodes_meta.jsonl`` bytes, in session-name order. Two runs whose manifest hash and config hashes
agree were trained on the same data under the same configuration; two that differ are not comparable
without saying what changed (R5, 5.6).

Nothing here touches hardware and nothing sends a command (R1, R2).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import time
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Any

import numpy as np
import torch
from lerobot.datasets.utils import INFO_PATH
from torch.utils.data import DataLoader, Sampler

from policy._shared import dataset_stats, set_torch_threads
from policy.act import ACTSpec, GoalACTPolicy
from policy.dataset import CellPair, LudoDataset, split_cell_pairs
from policy.diffusion import GoalDiffusionPolicy, PolicySpec
from policy.train_io import (
    BYTES_PER_PARAMETER,
    CHECKPOINT_COPIES,
    CHECKPOINT_FILE,
    CHECKPOINT_MARGIN,
    EMA,
    STEP_CHECKPOINT_GLOB,
    DiskGuardError,
    atomic_save,
    check_checkpoint_disk,
    checkpoint_bytes,
    prune_step_checkpoints,
    write_step_checkpoint,
)
from runtime import config
from runtime.log import get_logger
from runtime.safety import REPO_ROOT
from teleop.recorder import SIDECAR

__all__ = [
    "CHECKPOINT_DIR",
    "CHECKPOINT_FILE",
    "POLICIES",
    "STEP_CHECKPOINT_GLOB",
    "DiskGuardError",
    "EMA",
    "InjectedFault",
    "atomic_save",
    "check_checkpoint_disk",
    "checkpoint_bytes",
    "dataset_manifest_hash",
    "git_commit",
    "lr_multiplier",
    "main",
    "policy_kind",
    "prune_step_checkpoints",
    "train",
    "warmup_for",
    "write_step_checkpoint",
]

#: ``--policy NAME`` -> (the spec it builds, the model it trains, the ``config/training.yaml`` block).
#: The spec type is what :func:`train` dispatches on, so a caller that passes a spec directly (the
#: tests do) never has to name the policy twice.
POLICIES: dict[str, tuple[type, type]] = {
    "diffusion": (PolicySpec, GoalDiffusionPolicy),
    "act": (ACTSpec, GoalACTPolicy),
}

#: Where a run's directory is created (``compute.checkpoint_dir``), relative to the repo root.
CHECKPOINT_DIR = REPO_ROOT / "data" / "checkpoints"
#: Heartbeat directory of section 7, for a run that lasts hours.
LOG_DIR = REPO_ROOT / "data" / "logs"
#: The files whose bytes make up a session's contribution to the manifest hash.
MANIFEST_FILES: tuple[str, ...] = (INFO_PATH, SIDECAR)
#: ``--smoke``: the shortest run that still proves the loop trains (T-029).
SMOKE_STEPS, SMOKE_BATCH = 30, 4
#: The warmup is never more than this fraction of a run: a 500-step warmup inside a 30-step smoke run
#: would train at a fifteenth of the learning rate and prove nothing (:func:`warmup_for`).
WARMUP_MAX_FRACTION = 0.1
#: The seed every validation pass draws its diffusion noise and timesteps from. Fixed, so two
#: validation losses at two different steps differ by the model and not by the draw.
VAL_SEED = 20260912
#: ROLL episodes address no cell pair, so no pair split can hold them out; they stay in training
#: (T-027, ``policy/dataset.py`` ``split``).
NO_PAIR: CellPair = (None, None)


class InjectedFault(RuntimeError):
    """``--fault-at-step``: the deliberate crash the resume test needs. Never raised without it."""


def policy_kind(spec: PolicySpec | ACTSpec) -> str:
    """Which entry of :data:`POLICIES` this spec names: the model, the config block and the run name."""
    for name, (spec_type, _model) in POLICIES.items():
        if isinstance(spec, spec_type):
            return name
    raise TypeError(f"{type(spec).__name__} is not a policy spec; known: {sorted(POLICIES)}")


def dataset_manifest_hash(sessions: Sequence[Path | str]) -> str:
    """sha256 over every session's ``meta/info.json`` and ``episodes_meta.jsonl``, in name order."""
    digest = hashlib.sha256()
    for session in sorted((Path(s) for s in sessions), key=lambda p: p.name):
        digest.update(session.name.encode("utf-8"))
        for relative in MANIFEST_FILES:
            path = session / relative
            if not path.is_file():
                raise FileNotFoundError(f"{path} is missing: {session} is not a session written by teleop/recorder.py")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def git_commit() -> str | None:
    """``git rev-parse HEAD``, or None outside a checkout: a checkpoint names the code that made it."""
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None


# --------------------------------------------------------------------------------------------------
# the schedule: a linear warmup, then a cosine decay (T-035)
# --------------------------------------------------------------------------------------------------


def warmup_for(warmup_steps: int, total_steps: int) -> int:
    """The warmup actually used: ``warmup_steps``, clamped to :data:`WARMUP_MAX_FRACTION` of the run.

    The configured 500 steps are a tenth of a percent of a 200 000-step run and five sixths of the
    30-step smoke run. Clamping is what lets one configured value serve both without a smoke run
    spending its whole budget below the learning rate it is meant to prove.
    """
    if int(warmup_steps) <= 0:
        return 0
    return max(1, min(int(warmup_steps), int(total_steps * WARMUP_MAX_FRACTION)))


def lr_multiplier(step: int, *, warmup: int, total: int, min_ratio: float = 0.0) -> float:
    """Learning-rate factor at 0-based optimiser ``step`` (0 is the first step of the run).

    ``warmup`` linear increments of ``1 / warmup`` -- so the first step trains at ``1 / warmup`` and
    step ``warmup - 1`` at the full rate -- then a cosine from 1 down to ``min_ratio``, reaching it at
    the run's **last** step, ``total - 1`` (steps here are 0-based, so a run of ``total`` steps uses
    indices 0 .. total - 1 and a schedule that only reached its floor at index ``total`` would never
    reach it at all). Continuous at the join: the multiplier is 1.0 at ``warmup`` from either side.
    """
    if total < 1:
        raise ValueError(f"total must be >= 1, got {total}")
    if step < 0:
        raise ValueError(f"step must be >= 0, got {step}")
    if warmup > 0 and step < warmup:
        return (step + 1) / warmup
    progress = min(1.0, max(0.0, (step - warmup) / max(1, total - warmup - 1)))
    return float(min_ratio + (1.0 - min_ratio) * 0.5 * (1.0 + math.cos(math.pi * progress)))


# --------------------------------------------------------------------------------------------------
# the batch stream: reproducible from (seed, step), so a resume continues it exactly
# --------------------------------------------------------------------------------------------------


class _StepSampler(Sampler[int]):
    """An endless shuffled index stream positioned by optimiser step, not by epoch.

    ``DataLoader(shuffle=True)`` draws a fresh permutation from the global RNG each time its iterator
    is created, so a resumed run cannot land where the first one stopped without replaying every
    batch before it. Here epoch *e*'s permutation is ``default_rng([seed, e])`` and the resume point
    is arithmetic: ``start_step * batch_size`` indices in. With ``drop_last=True`` every batch is
    exactly ``batch_size`` samples and batches straddle epoch boundaries, which is what makes the
    step count, and not the epoch, the unit of the run.
    """

    def __init__(self, size: int, batch_size: int, seed: int, start_step: int = 0) -> None:
        if size < 1:
            raise ValueError("cannot sample from an empty dataset")
        self.size, self.batch_size, self.seed = int(size), int(batch_size), int(seed)
        self.consumed = int(start_step) * int(batch_size)

    def __iter__(self) -> Iterator[int]:
        epoch, offset = divmod(self.consumed, self.size)
        while True:
            order = np.random.default_rng([self.seed, epoch]).permutation(self.size)
            yield from (int(i) for i in order[offset:])
            epoch, offset = epoch + 1, 0


def _validation_loss(model: torch.nn.Module, loader: DataLoader, batches: int, device: str) -> float:
    """Mean loss over the first ``batches`` validation batches, at a fixed noise draw.

    Both models draw randomness inside their loss (the diffusion timestep and epsilon; ACT's dropout
    and VAE sampling in training mode), so a validation loss taken from the training RNG stream would
    move with the draw as much as with the model. The global RNG is therefore seeded with
    :data:`VAL_SEED` for the pass and **restored afterwards**, which also keeps the training stream
    -- and therefore a resumed run -- bit-identical to a run that validated at different steps.
    """
    state = torch.get_rng_state()
    was_training = model.training
    model.eval()
    torch.manual_seed(VAL_SEED)
    total, count = 0.0, 0
    try:
        with torch.no_grad():
            for batch in islice(loader, max(int(batches), 1)):
                moved = {key: value.to(device) for key, value in batch.items()}
                total += float(model(moved))
                count += 1
    finally:
        model.train(was_training)
        torch.set_rng_state(state)
    return total / max(count, 1)


def _split(sessions: Sequence[Path | str], val_fraction: float, seed: int) -> tuple[list[CellPair] | None,
                                                                                   list[CellPair]]:
    """``(train_split, held_out_pairs)`` for :class:`policy.dataset.LudoDataset` ``split``.

    ``val_fraction`` 0 means no split at all (``None`` keeps every episode). Otherwise the training
    split is the training pairs **plus** ``(None, None)``, which is how the ROLL episodes -- which
    have no cell pair to hold out -- stay in training (T-027).
    """
    if float(val_fraction) <= 0.0:
        return None, []
    train_pairs, held_out = split_cell_pairs(sessions, float(val_fraction), seed)
    return [*train_pairs, NO_PAIR], held_out


def train(
    sessions: Sequence[Path | str],
    *,
    steps: int,
    batch_size: int,
    learning_rate: float,
    weight_decay: float,
    seed: int = 0,
    device: str = "cpu",
    out_dir: Path | str | None = None,
    log_dir: Path | str | None = None,
    run_name: str | None = None,
    spec: PolicySpec | ACTSpec | None = None,
    config_root: Path | str | None = None,
    augment: bool = True,
    stats_samples: int | None = None,
    workers: int = 0,
    log_every: int = 1,
    config_block: str | None = None,
    ema_decay: float | None = None,
    warmup_steps: int | None = None,
    lr_min_ratio: float | None = None,
    val_fraction: float = 0.0,
    val_every: int | None = None,
    val_batches: int | None = None,
    resume: Path | str | None = None,
    stop_after: int | None = None,
    checkpoint_every: int | None = None,
    keep_last: int | None = None,
    disk_guard: bool = True,
    disk_guard_factor: float | None = None,
    fault_at_step: int | None = None,
) -> dict:
    """Run ``steps`` optimiser steps and write the run directory. Returns the run record."""
    if steps < 1:
        raise ValueError(f"steps must be >= 1, got {steps}")
    log = get_logger("policy.train")
    torch.manual_seed(int(seed))
    training = config.load("training", root=config_root)
    spec = PolicySpec.from_config(config_root) if spec is None else spec
    kind = policy_kind(spec)
    block = training[config_block or kind]
    samples = int(block["stats_samples"]) if stats_samples is None else int(stats_samples)
    decay = float(block["ema_decay"]) if ema_decay is None else float(ema_decay)
    warmup = warmup_for(int(block["warmup_steps"]) if warmup_steps is None else int(warmup_steps), steps)
    min_ratio = float(block["lr_min_ratio"]) if lr_min_ratio is None else float(lr_min_ratio)
    every = int(block["val_every"]) if val_every is None else int(val_every)
    val_count = int(block["val_batches"]) if val_batches is None else int(val_batches)
    compute = training["compute"]
    save_every = int(compute["checkpoint_every"]) if checkpoint_every is None else int(checkpoint_every)
    keep = int(compute["keep_last"]) if keep_last is None else int(keep_last)
    guard_factor = float(compute["disk_guard_factor"]) if disk_guard_factor is None else float(disk_guard_factor)

    # Each model of 5.7 gets the observation history its own config block asks for: `obs_history` is
    # 2 for the Diffusion Policy and 1 for ACT, which lerobot forbids from taking more (T-034).
    train_split, held_out = _split(sessions, val_fraction, seed)
    shared = {"chunk": spec.chunk, "n_obs_steps": spec.n_obs_steps, "config_root": config_root}
    data = LudoDataset(sessions, augment=augment, seed=seed, split=train_split, **shared)
    if len(data) == 0:
        raise ValueError(
            f"the training split is empty: val_fraction {val_fraction} held out every cell pair of "
            f"{[str(s) for s in sessions]}. Lower --val-fraction or collect more cell pairs."
        )
    validation = LudoDataset(sessions, augment=False, seed=seed, split=held_out, **shared) if held_out else None
    if validation is not None and len(validation) == 0:
        validation = None
    manifest = dataset_manifest_hash(sessions)
    hashes = {name: config.config_hash(name, config_root) for name in config.NAMES}
    log.info("train_start", policy=kind, block=config_block or kind, steps=steps, batch=batch_size,
             device=device, frames=len(data), episodes=len(data.episodes), n_obs_steps=data.n_obs_steps,
             val_frames=0 if validation is None else len(validation), val_pairs=len(held_out),
             warmup=warmup, ema_decay=decay, training_config_hash=hashes["training"],
             dataset_manifest=manifest)

    model = POLICIES[kind][1](spec, device=device).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(learning_rate), weight_decay=float(weight_decay))
    ema = EMA(model, decay)
    start_step, losses, elapsed_before = 0, [], 0.0
    if resume is None:
        model.norm.load_stats(dataset_stats(data, samples=samples, seed=seed))
    else:
        # The statistics, the weights, the optimiser, the average, the RNG and the curve so far all
        # come from the checkpoint: a resumed run must not re-estimate anything the first one fixed.
        payload = torch.load(Path(resume), map_location=device, weights_only=True)
        if payload.get("policy") != kind or payload.get("spec") != spec.to_dict():
            raise ValueError(
                f"{resume} holds a {payload.get('policy')!r} checkpoint of a different architecture; "
                f"resume needs the same policy and the same spec (this run is {kind!r})"
            )
        model.load_state_dict(payload["state_dict"])
        optimizer.load_state_dict(payload["optimizer"])
        ema.load_state_dict(payload["ema_state_dict"], int(payload["ema_step"]))
        start_step = int(payload["step"])
        losses = [tuple(row) for row in payload["losses"]]
        elapsed_before = float(losses[-1][3]) if losses else 0.0
        torch.set_rng_state(payload["rng"].cpu().to(torch.uint8))
        if start_step >= steps:
            raise ValueError(f"{resume} is already at step {start_step}; --steps must exceed it (got {steps})")
        log.info("train_resume", checkpoint=str(resume), step=start_step, of=steps)
    model.train()

    sampler = _StepSampler(len(data), int(batch_size), int(seed), start_step=start_step)
    # Every DataLoader iterator draws its worker base seed from `generator`, and from the **global**
    # RNG when that is None (`_BaseDataLoaderIter.__init__`). A resumed run creates its iterator after
    # restoring the RNG state, so that one draw would shift the loss stream by one and a resume would
    # stop being exact -- measured: it moved the losses by ~0.1. The generator takes it out of the
    # global stream, where nothing in this loop but the model's own loss draws any more.
    generator = torch.Generator().manual_seed(int(seed))
    loader = DataLoader(data, batch_size=int(batch_size), sampler=sampler, num_workers=int(workers),
                        drop_last=True, generator=generator)
    val_loader = (
        None if validation is None
        else DataLoader(validation, batch_size=int(batch_size), shuffle=False, num_workers=int(workers),
                        generator=torch.Generator().manual_seed(int(seed)))
    )
    run = run_name or f"{datetime.now().strftime('%Y%m%dT%H%M%S')}_{config_block or kind}"
    directory = Path(CHECKPOINT_DIR if out_dir is None else out_dir) / run
    directory.mkdir(parents=True, exist_ok=True)
    heartbeat = Path(LOG_DIR if log_dir is None else log_dir) / f"train_{run}.heartbeat"
    heartbeat.parent.mkdir(parents=True, exist_ok=True)

    # `steps` is the horizon of the whole run -- the schedule is computed against it -- and
    # `stop_after` is how many of them this invocation runs before checkpointing and returning. A
    # 200 000-step run on a preemptible box is the same run whether it is taken in one slice or ten
    # (``--stop-after`` + ``--resume``), and the learning rate at step 120 000 is the same either way.
    last_step = steps if stop_after is None else min(steps, start_step + max(int(stop_after), 1))

    # T-036: the run must not start if its checkpoints cannot fit, and what every one of them will
    # cost is printed either way. Before the first step, because the point is to fail in a second
    # rather than after an hour of training that cannot be saved.
    parameters = int(sum(p.numel() for p in model.parameters()))
    disk = check_checkpoint_disk(directory, parameters, factor=guard_factor, enforce=disk_guard, log=log)

    # Everything a checkpoint records about *which run it belongs to*, known before the first step so
    # that a periodic checkpoint carries it too (policy/export.py reads these out of `run`).
    arguments = {"steps": steps, "batch_size": batch_size, "learning_rate": float(learning_rate),
                 "weight_decay": float(weight_decay), "seed": seed, "device": device, "augment": augment,
                 "stats_samples": samples, "workers": workers, "ema_decay": decay,
                 "warmup_steps": warmup, "lr_min_ratio": min_ratio, "val_fraction": float(val_fraction),
                 "val_every": every, "val_batches": val_count, "stop_after": stop_after,
                 "checkpoint_every": save_every, "keep_last": keep, "disk_guard": bool(disk_guard),
                 "disk_guard_factor": guard_factor,
                 "resumed_from": None if resume is None else str(resume), "resumed_at_step": start_step,
                 "stopped_at_step": last_step}
    identity = {
        "run": run,
        "policy": kind,
        "config_block": config_block or kind,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "config_hashes": hashes,
        "dataset_manifest_sha256": manifest,
        "spec": spec.to_dict(),
        "args": arguments,
        "parameters": parameters,
        "checkpoint_bytes_estimate": disk["estimate_bytes"],
    }

    def checkpoint_payload(record: dict) -> dict:
        """What ``--resume`` needs, plus the run record: the model, optimiser, EMA, step, RNG, curve."""
        return {"policy": kind, "spec": spec.to_dict(), "state_dict": model.state_dict(),
                "ema_state_dict": ema.state_dict(model), "ema_step": ema.step,
                "optimizer": optimizer.state_dict(), "step": losses[-1][0],
                "rng": torch.get_rng_state(), "losses": losses, "run": record}

    started = time.perf_counter()
    batches = islice(iter(loader), last_step - start_step)
    for step, batch in enumerate(batches, start=start_step + 1):
        factor = lr_multiplier(step - 1, warmup=warmup, total=steps, min_ratio=min_ratio)
        rate = float(learning_rate) * factor
        for group in optimizer.param_groups:
            group["lr"] = rate
        batch = {key: value.to(device) for key, value in batch.items()}
        loss = model(batch)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        ema.update(model)
        val = (
            _validation_loss(model, val_loader, val_count, device)
            if val_loader is not None and (step % max(every, 1) == 0 or step == last_step)
            else None
        )
        elapsed = elapsed_before + time.perf_counter() - started
        losses.append((step, float(loss.detach()), val, elapsed, rate))
        if step % max(int(log_every), 1) == 0 or step == last_step:
            log.info("train_step", step=step, loss=round(losses[-1][1], 6), lr=rate,
                     val_loss=None if val is None else round(val, 6), elapsed_s=round(elapsed, 2))
            heartbeat.write_text(f"step {step}/{steps} loss {losses[-1][1]:.6f} elapsed_s {elapsed:.1f}\n",
                                 encoding="utf-8")
        # The periodic checkpoint (T-036). Not at `last_step`, which is checkpointed below anyway as
        # the end of the invocation; a run killed between two of these resumes from the last one.
        if save_every > 0 and step % save_every == 0 and step != last_step:
            alias, pruned = write_step_checkpoint(
                checkpoint_payload({**identity, "step": step, "of": steps, "complete": False,
                                    "loss_last": losses[-1][1], "elapsed_s": elapsed}),
                directory, step, keep)
            log.info("checkpoint", step=step, file=alias.name, keep_last=keep,
                     pruned=[p.name for p in pruned])
        # The deliberate crash of `--fault-at-step`, after everything step N would have done. It is a
        # test aid (tests/test_train.py kills a real run here and resumes it); without the flag
        # `fault_at_step` is None and this is dead code.
        if fault_at_step is not None and step >= int(fault_at_step):
            raise InjectedFault(f"--fault-at-step {int(fault_at_step)}: crashing after step {step} of "
                                f"{steps} on purpose; the last checkpoint is the run's own")

    with (directory / "loss.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["step", "loss", "val_loss", "lr", "elapsed_s"])
        writer.writerows([(s, f"{loss:.6f}", "" if val is None else f"{val:.6f}", f"{rate:.12g}", f"{t:.3f}")
                          for s, loss, val, t, rate in losses])

    validated = [(s, val) for s, _loss, val, _t, _lr in losses if val is not None]
    record = {
        **identity,
        "complete": True,
        "sessions": [
            {"path": str(Path(s)), "episodes": sum(1 for e in data.episodes if e.session == i)}
            for i, s in enumerate(sessions)
        ],
        "frames": len(data),
        "n_obs_steps": data.n_obs_steps,
        "validation": {
            "frames": 0 if validation is None else len(validation),
            "episodes": 0 if validation is None else len(validation.episodes),
            "held_out_pairs": [list(pair) for pair in held_out],
            "loss_first": validated[0][1] if validated else None,
            "loss_last": validated[-1][1] if validated else None,
        },
        "loss_first": losses[0][1],
        "loss_last": losses[-1][1],
        "loss_mean_last_10": float(np.mean([loss for _s, loss, _v, _t, _lr in losses[-10:]])),
        "elapsed_s": losses[-1][3],
        "step": losses[-1][0],
        "checkpoint": str(directory / CHECKPOINT_FILE),
    }
    (directory / "run.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # The end of this invocation is a checkpoint like any other: written to `.tmp` and renamed, so
    # that a crash here leaves the last periodic checkpoint rather than a truncated file (T-036).
    atomic_save(checkpoint_payload(record), directory / CHECKPOINT_FILE)
    log.info("train_end", run=run, policy=kind, loss_first=round(record["loss_first"], 6),
             loss_last=round(record["loss_last"], 6),
             val_loss_last=record["validation"]["loss_last"], elapsed_s=round(record["elapsed_s"], 1))
    return record


def main(argv: list[str] | None = None) -> int:
    """``python -m policy.train --sessions data/raw/<session> [--policy act] [--smoke]``."""
    set_torch_threads()  # D-020: the pool is a configured value, applied once, at process start
    training = config.load("training")
    parser = argparse.ArgumentParser(description="Train the Diffusion Policy or the ACT baseline (CLAUDE.md 5.7).")
    parser.add_argument("--sessions", nargs="+", required=True, metavar="DIR",
                        help="session directories under data/raw/ (teleop/recorder.py wrote them)")
    parser.add_argument("--policy", default="diffusion", choices=tuple(POLICIES),
                        help="which model of 5.7 to train on this data (default diffusion)")
    parser.add_argument("--config-block", default=None, metavar="NAME",
                        help="config/training.yaml block the spec and the defaults come from "
                             "(default: the policy name; 'diffusion_small' is the D-019 fallback)")
    parser.add_argument("--steps", type=int, default=None,
                        help="optimiser steps (default: the policy's train_iterations)")
    parser.add_argument("--batch-size", type=int, default=None, help="default: the policy's batch_size")
    parser.add_argument("--lr", type=float, default=None, help="default: the policy's learning_rate")
    parser.add_argument("--seed", type=int, default=None, help="dataset, init and stats seed (default: the policy's)")
    parser.add_argument("--device", default="cpu", choices=("cpu", "cuda"), help="default cpu")
    parser.add_argument("--workers", type=int, default=0, help="DataLoader workers (default 0)")
    parser.add_argument("--no-augment", action="store_true", help="turn off the augmentation of 5.7")
    parser.add_argument("--out-dir", default=None, help=f"run directory root (default {CHECKPOINT_DIR})")
    parser.add_argument("--run-name", default=None, help="run directory name (default <timestamp>_<block>)")
    parser.add_argument("--stats-samples", type=int, default=None,
                        help="samples for the normalisation statistics (default: the policy's stats_samples)")
    parser.add_argument("--smoke", action="store_true",
                        help=f"{SMOKE_STEPS} steps, batch {SMOKE_BATCH}, on CPU, no validation split: "
                             "proves the loop trains, nothing more")
    parser.add_argument("--image-hw", type=int, nargs=2, default=None, metavar=("H", "W"),
                        help="encoder input size (default: the policy's encoder_image_hw)")
    parser.add_argument("--val-fraction", type=float, default=None,
                        help="fraction of the cell pairs held out for the validation loss "
                             f"(default: dataset.val_fraction = {training['dataset']['val_fraction']}; 0 disables)")
    parser.add_argument("--val-every", type=int, default=None,
                        help="steps between validation passes (default: the policy's val_every)")
    parser.add_argument("--ema-decay", type=float, default=None, help="default: the policy's ema_decay")
    parser.add_argument("--warmup-steps", type=int, default=None, help="default: the policy's warmup_steps")
    parser.add_argument("--stop-after", type=int, default=None, metavar="N",
                        help="run at most N steps in this invocation, then checkpoint and exit; --resume "
                             "continues the same run on the same schedule")
    parser.add_argument("--resume", default=None, metavar="CHECKPOINT",
                        help="continue a run from its checkpoint.pt (weights, optimiser, EMA, step, RNG)")
    compute = training["compute"]
    parser.add_argument("--checkpoint-every", type=int, default=None, metavar="N",
                        help="write checkpoint.pt (and a checkpoint_step<N>.pt copy) every N steps, so "
                             "that a crash costs at most N steps; 0 writes only the final one "
                             f"(default: compute.checkpoint_every = {compute['checkpoint_every']})")
    parser.add_argument("--keep-last", type=int, default=None, metavar="K",
                        help="step checkpoints kept beside checkpoint.pt, newest first; an exported "
                             f"bundle is never pruned (default: compute.keep_last = {compute['keep_last']})")
    parser.add_argument("--no-disk-guard", action="store_true",
                        help="train even when the free space under the run directory is below "
                             f"compute.disk_guard_factor ({compute['disk_guard_factor']}) x the "
                             "estimated checkpoint size; the shortfall is logged (Q-002)")
    parser.add_argument("--fault-at-step", type=int, default=None, metavar="K",
                        help="TEST AID: raise after step K instead of finishing, to exercise a crash "
                             "and the resume from the last periodic checkpoint. Does nothing unless "
                             "passed; tests/test_train.py is the only caller")
    args = parser.parse_args(argv)

    name = args.config_block or args.policy
    spec = POLICIES[args.policy][0].from_config(block=name)  # refuses a block the file does not have
    block = training[name]
    if args.image_hw is not None:
        spec = replace(spec, image_hw=tuple(args.image_hw))
    steps = SMOKE_STEPS if args.smoke else (int(block["train_iterations"]) if args.steps is None else args.steps)
    batch = int(block["batch_size"]) if args.batch_size is None else args.batch_size
    batch_size = SMOKE_BATCH if args.smoke else batch
    device = "cpu" if args.smoke else args.device
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    run_name = args.run_name or (f"{stamp}_{name}_smoke" if args.smoke else None)
    # A smoke run holds nothing out: a three-episode mock session has one or two cell pairs, and
    # holding one out leaves a training set that proves nothing about the loop.
    default_fraction = 0.0 if args.smoke else float(training["dataset"]["val_fraction"])
    val_fraction = default_fraction if args.val_fraction is None else float(args.val_fraction)

    record: dict[str, Any] = train(
        args.sessions, steps=steps, batch_size=batch_size,
        learning_rate=float(block["learning_rate"]) if args.lr is None else args.lr,
        weight_decay=float(block["weight_decay"]),
        seed=int(block["seed"]) if args.seed is None else args.seed,
        device=device, out_dir=args.out_dir,
        run_name=run_name, spec=spec, augment=not args.no_augment, stats_samples=args.stats_samples,
        workers=args.workers, config_block=name, val_fraction=val_fraction, val_every=args.val_every,
        ema_decay=args.ema_decay, warmup_steps=args.warmup_steps, resume=args.resume,
        stop_after=args.stop_after, checkpoint_every=args.checkpoint_every, keep_last=args.keep_last,
        disk_guard=not args.no_disk_guard, fault_at_step=args.fault_at_step,
    )
    print(f"run {record['run']} ({record['policy']}, block {record['config_block']}): "
          f"{record['frames']} frames, {record['parameters'] / 1e6:.1f}M parameters")
    print(f"training config hash {record['config_hashes']['training']}")
    print(f"dataset manifest sha256 {record['dataset_manifest_sha256']}")
    # The disk guard's estimate, printed for every run (T-036). Recomputed from the parameter count
    # rather than read from the record, so that it is the same number the guard used and one key.
    print(f"checkpoint {checkpoint_bytes(record['parameters']) / 1e9:.2f} GB estimated "
          f"({record['parameters'] / 1e6:.1f}M parameters x {BYTES_PER_PARAMETER} bytes x "
          f"{CHECKPOINT_COPIES} + {CHECKPOINT_MARGIN:.0%})")
    print(f"loss step 1 {record['loss_first']:.6f} -> step {record['args']['stopped_at_step']} "
          f"{record['loss_last']:.6f} "
          f"(mean of the last 10: {record['loss_mean_last_10']:.6f}) in {record['elapsed_s']:.1f} s")
    validation = record["validation"]
    if validation["loss_last"] is not None:
        print(f"validation loss {validation['loss_first']:.6f} -> {validation['loss_last']:.6f} on "
              f"{validation['frames']} frames of {len(validation['held_out_pairs'])} held-out cell pairs")
    print(f"written {Path(record['checkpoint']).parent}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
