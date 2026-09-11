"""Train the Diffusion Policy, or the ACT baseline, of CLAUDE.md 5.7 on recorded sessions.

```bash
.venv/bin/python -m policy.train --sessions data/raw/20260912T090000 --steps 200000
.venv/bin/python -m policy.train --sessions data/raw/20260912T090000 --policy act   # the baseline
.venv/bin/python -m policy.train --sessions data/raw/mock_smoke --smoke      # 30 steps, batch 4, CPU
```

Real training happens on Greennode (5.8, Q-001); this is the entry point ``cloud/greennode.sh``
launches there and the one that runs on the laptop for a smoke test. It is a plain torch loop over
:class:`policy.dataset.LudoDataset` with the model's own loss -- no lerobot trainer, no hub, no EMA
(``diffusion.ema_decay`` is not applied yet; see docs/policy.md).

``--policy act`` is the whole difference between the two models of 5.7: the same sessions, the same
:class:`~policy.dataset.LudoDataset` (at ``act.chunk`` and ``act.obs_history`` instead of the diffusion
block's), the same split, the same statistics, the same loop, the same run directory. CLAUDE.md 5.7
requires the baseline to be trained on every dataset the primary is trained on, so training it must
cost one flag and must not be able to change anything else. Defaults (steps, batch, lr, weight decay,
seed) come from the chosen policy's block in ``config/training.yaml``.

Every run writes ``data/checkpoints/<run>/``:

======================  ==========================================================================
``run.json``            args, git commit, the six config hashes, the dataset manifest hash, the
                        per-session episode and frame counts, the final loss and the wall time
``loss.csv``            ``step,loss,elapsed_s`` for every step: the loss curve section 8 audits
``checkpoint.pt``       ``{"policy", "spec", "state_dict", "run"}``; ``policy/export.py`` turns it
                        into a bundle
======================  ==========================================================================

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
import subprocess
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Any

import numpy as np
import torch
from lerobot.datasets.utils import INFO_PATH
from torch.utils.data import DataLoader

from policy._shared import dataset_stats
from policy.act import ACTSpec, GoalACTPolicy
from policy.dataset import LudoDataset
from policy.diffusion import GoalDiffusionPolicy, PolicySpec
from runtime import config
from runtime.log import get_logger
from runtime.safety import REPO_ROOT
from teleop.recorder import SIDECAR

__all__ = ["CHECKPOINT_DIR", "POLICIES", "dataset_manifest_hash", "git_commit", "main", "policy_kind", "train"]

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


def _batches(loader: DataLoader):
    """An endless stream of batches: a run is counted in steps, not in epochs."""
    while True:
        yield from loader


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
) -> dict:
    """Run ``steps`` optimiser steps and write the run directory. Returns the run record."""
    if steps < 1:
        raise ValueError(f"steps must be >= 1, got {steps}")
    log = get_logger("policy.train")
    torch.manual_seed(int(seed))
    training = config.load("training", root=config_root)
    spec = PolicySpec.from_config(config_root) if spec is None else spec
    kind = policy_kind(spec)
    samples = int(training[kind]["stats_samples"]) if stats_samples is None else int(stats_samples)

    # Each model of 5.7 gets the observation history its own config block asks for: `obs_history` is
    # 2 for the Diffusion Policy and 1 for ACT, which lerobot forbids from taking more (T-034).
    data = LudoDataset(sessions, chunk=spec.chunk, n_obs_steps=spec.n_obs_steps, augment=augment,
                       seed=seed, config_root=config_root)
    manifest = dataset_manifest_hash(sessions)
    hashes = {name: config.config_hash(name, config_root) for name in config.NAMES}
    log.info("train_start", policy=kind, steps=steps, batch=batch_size, device=device, frames=len(data),
             episodes=len(data.episodes), n_obs_steps=data.n_obs_steps,
             training_config_hash=hashes["training"], dataset_manifest=manifest)

    model = POLICIES[kind][1](spec, device=device).to(device)
    model.norm.load_stats(dataset_stats(data, samples=samples, seed=seed))
    model.train()
    optimizer = torch.optim.Adam(model.parameters(), lr=float(learning_rate), weight_decay=float(weight_decay))

    loader = DataLoader(data, batch_size=int(batch_size), shuffle=True, num_workers=int(workers), drop_last=False)
    run = run_name or f"{datetime.now().strftime('%Y%m%dT%H%M%S')}_{kind}"
    directory = Path(CHECKPOINT_DIR if out_dir is None else out_dir) / run
    directory.mkdir(parents=True, exist_ok=True)
    heartbeat = Path(LOG_DIR if log_dir is None else log_dir) / f"train_{run}.heartbeat"
    heartbeat.parent.mkdir(parents=True, exist_ok=True)

    losses: list[tuple[int, float, float]] = []
    started = time.perf_counter()
    for step, batch in enumerate(islice(_batches(loader), steps), start=1):
        batch = {key: value.to(device) for key, value in batch.items()}
        loss = model(batch)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
        elapsed = time.perf_counter() - started
        losses.append((step, float(loss.detach()), elapsed))
        if step % max(int(log_every), 1) == 0 or step == steps:
            log.info("train_step", step=step, loss=round(losses[-1][1], 6), elapsed_s=round(elapsed, 2))
            heartbeat.write_text(f"step {step}/{steps} loss {losses[-1][1]:.6f} elapsed_s {elapsed:.1f}\n",
                                 encoding="utf-8")

    with (directory / "loss.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["step", "loss", "elapsed_s"])
        writer.writerows([(s, f"{loss:.6f}", f"{t:.3f}") for s, loss, t in losses])

    record = {
        "run": run,
        "policy": kind,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "config_hashes": hashes,
        "dataset_manifest_sha256": manifest,
        "sessions": [
            {"path": str(Path(s)), "episodes": sum(1 for e in data.episodes if e.session == i)}
            for i, s in enumerate(sessions)
        ],
        "frames": len(data),
        "n_obs_steps": data.n_obs_steps,
        "spec": spec.to_dict(),
        "args": {"steps": steps, "batch_size": batch_size, "learning_rate": float(learning_rate),
                 "weight_decay": float(weight_decay), "seed": seed, "device": device, "augment": augment,
                 "stats_samples": samples, "workers": workers},
        "parameters": int(sum(p.numel() for p in model.parameters())),
        "loss_first": losses[0][1],
        "loss_last": losses[-1][1],
        "loss_mean_last_10": float(np.mean([loss for _s, loss, _t in losses[-10:]])),
        "elapsed_s": losses[-1][2],
        "checkpoint": str(directory / "checkpoint.pt"),
    }
    (directory / "run.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    torch.save({"policy": kind, "spec": spec.to_dict(), "state_dict": model.state_dict(), "run": record},
               directory / "checkpoint.pt")
    log.info("train_end", run=run, policy=kind, loss_first=round(record["loss_first"], 6),
             loss_last=round(record["loss_last"], 6), elapsed_s=round(record["elapsed_s"], 1))
    return record


def main(argv: list[str] | None = None) -> int:
    """``python -m policy.train --sessions data/raw/<session> [--policy act] [--smoke]``."""
    training = config.load("training")
    parser = argparse.ArgumentParser(description="Train the Diffusion Policy or the ACT baseline (CLAUDE.md 5.7).")
    parser.add_argument("--sessions", nargs="+", required=True, metavar="DIR",
                        help="session directories under data/raw/ (teleop/recorder.py wrote them)")
    parser.add_argument("--policy", default="diffusion", choices=tuple(POLICIES),
                        help="which model of 5.7 to train on this data (default diffusion)")
    parser.add_argument("--steps", type=int, default=None,
                        help="optimiser steps (default: the policy's train_iterations)")
    parser.add_argument("--batch-size", type=int, default=None, help="default: the policy's batch_size")
    parser.add_argument("--lr", type=float, default=None, help="default: the policy's learning_rate")
    parser.add_argument("--seed", type=int, default=None, help="dataset, init and stats seed (default: the policy's)")
    parser.add_argument("--device", default="cpu", choices=("cpu", "cuda"), help="default cpu")
    parser.add_argument("--workers", type=int, default=0, help="DataLoader workers (default 0)")
    parser.add_argument("--no-augment", action="store_true", help="turn off the augmentation of 5.7")
    parser.add_argument("--out-dir", default=None, help=f"run directory root (default {CHECKPOINT_DIR})")
    parser.add_argument("--run-name", default=None, help="run directory name (default <timestamp>_<policy>)")
    parser.add_argument("--stats-samples", type=int, default=None,
                        help="samples for the normalisation statistics (default: the policy's stats_samples)")
    parser.add_argument("--smoke", action="store_true",
                        help=f"{SMOKE_STEPS} steps, batch {SMOKE_BATCH}, on CPU: proves the loop trains, nothing more")
    parser.add_argument("--image-hw", type=int, nargs=2, default=None, metavar=("H", "W"),
                        help="encoder input size (default: the policy's encoder_image_hw)")
    args = parser.parse_args(argv)

    block = training[args.policy]
    spec = POLICIES[args.policy][0].from_config()
    if args.image_hw is not None:
        spec = replace(spec, image_hw=tuple(args.image_hw))
    steps = SMOKE_STEPS if args.smoke else (int(block["train_iterations"]) if args.steps is None else args.steps)
    batch = int(block["batch_size"]) if args.batch_size is None else args.batch_size
    batch_size = SMOKE_BATCH if args.smoke else batch
    device = "cpu" if args.smoke else args.device
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    run_name = args.run_name or (f"{stamp}_{args.policy}_smoke" if args.smoke else None)

    record: dict[str, Any] = train(
        args.sessions, steps=steps, batch_size=batch_size,
        learning_rate=float(block["learning_rate"]) if args.lr is None else args.lr,
        weight_decay=float(block["weight_decay"]),
        seed=int(block["seed"]) if args.seed is None else args.seed,
        device=device, out_dir=args.out_dir,
        run_name=run_name, spec=spec, augment=not args.no_augment, stats_samples=args.stats_samples,
        workers=args.workers,
    )
    print(f"run {record['run']} ({record['policy']}): {record['frames']} frames, "
          f"{record['parameters'] / 1e6:.1f}M parameters")
    print(f"training config hash {record['config_hashes']['training']}")
    print(f"dataset manifest sha256 {record['dataset_manifest_sha256']}")
    print(f"loss step 1 {record['loss_first']:.6f} -> step {steps} {record['loss_last']:.6f} "
          f"(mean of the last 10: {record['loss_mean_last_10']:.6f}) in {record['elapsed_s']:.1f} s")
    print(f"written {Path(record['checkpoint']).parent}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
