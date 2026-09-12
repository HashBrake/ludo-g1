"""Checkpoint I/O and the weight EMA for :mod:`policy.train` (T-042, D-013).

Split unchanged out of ``policy/train.py``: the atomic write, the periodic step copies and their
pruning, the disk guard of T-036, and the EMA of T-035. They are what a run *writes*, as opposed to
the training loop itself, and ``policy/export.py`` reads the same files back.

Nothing here touches the robot: a checkpoint is a file (R1, R2). ``policy/train.py`` imports every
name back, so ``policy.train.EMA``, ``policy.train.atomic_save`` and the rest still resolve.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import torch

__all__ = [
    "CHECKPOINT_FILE",
    "STEP_CHECKPOINT_GLOB",
    "DiskGuardError",
    "EMA",
    "atomic_save",
    "check_checkpoint_disk",
    "checkpoint_bytes",
    "prune_step_checkpoints",
    "write_step_checkpoint",
]

#: The run's latest checkpoint, and the periodic copies kept beside it (T-036). A step checkpoint is
#: an alias of ``checkpoint.pt`` as it stood at that step, so ``--resume`` takes either name.
CHECKPOINT_FILE = "checkpoint.pt"
STEP_CHECKPOINT = "checkpoint_step{step}.pt"
STEP_CHECKPOINT_GLOB = "checkpoint_step*.pt"
#: Every checkpoint is written to ``<name>.tmp`` first and renamed over the old one, so that a crash
#: during the write leaves the previous checkpoint whole. The suffix is not ``.pt``, so a half-written
#: file can never be mistaken for a checkpoint by the glob above or by ``--resume``.
TMP_SUFFIX = ".tmp"
#: What a checkpoint costs per parameter: fp32 weights, their EMA, and Adam's two moments.
BYTES_PER_PARAMETER, CHECKPOINT_COPIES = 4, 4
#: Added to that estimate for everything else in the payload (the normalisation buffers, the loss
#: history, the run record) and for the filesystem's rounding.
CHECKPOINT_MARGIN = 0.1


class DiskGuardError(RuntimeError):
    """Not enough free space under the run directory for this run's checkpoints (Q-002)."""



# --------------------------------------------------------------------------------------------------
# checkpoints: atomic writes, periodic copies, pruning, and the disk guard (T-036)
# --------------------------------------------------------------------------------------------------


def checkpoint_bytes(parameters: int) -> int:
    """Estimated size of one ``checkpoint.pt`` for a model of ``parameters`` parameters.

    ``parameters x 4 bytes x 4`` -- the weights, their EMA, and Adam's two moments, all fp32 -- plus
    :data:`CHECKPOINT_MARGIN`. Measured against the real thing at the test scale: 38.4 M parameters
    wrote 614.8 MB and this estimate is 675.8 MB, so it is an over-estimate by design (a guard that
    under-estimates is not a guard).
    """
    return int(int(parameters) * BYTES_PER_PARAMETER * CHECKPOINT_COPIES * (1.0 + CHECKPOINT_MARGIN))


def check_checkpoint_disk(directory: Path | str, parameters: int, *, factor: float,
                          enforce: bool = True, log: Any | None = None) -> dict:
    """Refuse to start a run whose checkpoints will not fit. Returns the numbers either way.

    ``factor`` (``compute.disk_guard_factor``) times :func:`checkpoint_bytes` must be free under
    ``directory``: an atomic write holds the new checkpoint and the one it is replacing at the same
    time, and a run that fills the disk half way through loses the run *and* the checkpoint it was
    overwriting. ``enforce=False`` (``--no-disk-guard``) logs the shortfall and continues; this is a
    training-time convenience, not a safety rule (R3 is elsewhere and is never overridable).
    """
    estimate = checkpoint_bytes(parameters)
    needed = int(float(factor) * estimate)
    free = shutil.disk_usage(Path(directory)).free
    numbers = {"parameters": int(parameters), "estimate_bytes": estimate, "required_bytes": needed,
               "free_bytes": int(free), "factor": float(factor), "enforced": bool(enforce)}
    if log is not None:
        log.info("disk_guard", ok=free >= needed, **numbers)
    if free >= needed:
        return numbers
    message = (
        f"{Path(directory)} has {free / 1e9:.2f} GB free and this run needs "
        f"{needed / 1e9:.2f} GB for its checkpoints: one checkpoint of {int(parameters) / 1e6:.1f}M "
        f"parameters is about {estimate / 1e9:.2f} GB (parameters x {BYTES_PER_PARAMETER} bytes x "
        f"{CHECKPOINT_COPIES} for the weights, their EMA and Adam's two moments, plus "
        f"{CHECKPOINT_MARGIN:.0%}), times disk_guard_factor {factor:g} so that writing a new one "
        f"never destroys the one already on disk. This is agents/QUESTIONS.md Q-002 (12 GB free on "
        f"/home against the 500 GB the brief asks for): train on Greennode (5.8), point --out-dir at "
        f"a bigger disk, use --config-block diffusion_small, or pass --no-disk-guard to train anyway."
    )
    if enforce:
        raise DiskGuardError(message)
    if log is not None:
        log.warning("disk_guard_overridden", reason=message, **numbers)
    return numbers


def atomic_save(payload: dict, path: Path) -> Path:
    """``torch.save`` to ``<path>.tmp``, then :func:`os.replace` over ``path``.

    The rename is atomic on the same filesystem, so a crash (or a full disk) during the write leaves
    the previous checkpoint intact instead of a truncated file that ``--resume`` would choke on.
    """
    tmp = path.with_name(path.name + TMP_SUFFIX)
    torch.save(payload, tmp)
    os.replace(tmp, path)
    return path


def _step_of(path: Path) -> int:
    """``checkpoint_step1200.pt`` -> 1200."""
    return int(path.stem.rsplit("step", 1)[-1])


def prune_step_checkpoints(directory: Path | str, keep: int) -> list[Path]:
    """Delete all but the ``keep`` newest ``checkpoint_step<N>.pt``. Returns what was deleted.

    Only step checkpoints are ever considered: ``checkpoint.pt`` (the latest) and an exported bundle
    sitting in the same run directory are not matched by :data:`STEP_CHECKPOINT_GLOB` and cannot be
    pruned. ``keep <= 0`` keeps none of them, and ``checkpoint.pt`` still holds the latest state.
    """
    files = sorted(Path(directory).glob(STEP_CHECKPOINT_GLOB), key=_step_of)
    doomed = files if int(keep) <= 0 else files[:-int(keep)]
    for path in doomed:
        path.unlink(missing_ok=True)
    return doomed


def write_step_checkpoint(payload: dict, directory: Path | str, step: int,
                          keep: int) -> tuple[Path, list[Path]]:
    """One periodic checkpoint: ``checkpoint.pt`` updated atomically, aliased, older ones pruned.

    The step copy is a **hard link** to the file just written, not a second ``torch.save``: a 4.7 GB
    checkpoint is not written twice, and ``keep`` copies of it do not cost ``keep`` times the disk
    while they share an inode. Both names are replaced atomically, and unlinking either leaves the
    other whole. Returns ``(the step checkpoint, the ones deleted)``.
    """
    directory = Path(directory)
    latest = atomic_save(payload, directory / CHECKPOINT_FILE)
    alias = directory / STEP_CHECKPOINT.format(step=int(step))
    staged = alias.with_name(alias.name + TMP_SUFFIX)
    staged.unlink(missing_ok=True)
    try:
        os.link(latest, staged)
    except OSError:  # a filesystem without hard links (or across one): pay for the copy
        shutil.copy2(latest, staged)
    os.replace(staged, alias)
    return alias, prune_step_checkpoints(directory, keep)


# --------------------------------------------------------------------------------------------------
# EMA of the weights (5.7 `ema_decay`), which is what a bundle exports by default
# --------------------------------------------------------------------------------------------------


class EMA:
    """Exponential moving average of a model's parameters, updated after every optimiser step.

    ``shadow <- d * shadow + (1 - d) * parameter``, with ``d = min(decay, (1 + n) / (10 + n))`` at
    update ``n``. The ramp is what makes the average usable early: at ``decay`` 0.9999 the plain
    recursion has a 10 000-step time constant, so without it the "average" of a 30-step smoke run
    would still be the initialisation and every short run would export noise.

    Only parameters are averaged. Buffers -- the normalisation statistics of
    :class:`policy._shared.Normalizer` and the BatchNorm running statistics of the ResNet-18
    encoders -- are taken from the live model at export time: they are already running averages of
    the data, and averaging them twice makes them lag the weights they belong to.
    """

    def __init__(self, model: torch.nn.Module, decay: float, *, step: int = 0) -> None:
        if not 0.0 < float(decay) < 1.0:
            raise ValueError(f"EMA decay must be in (0, 1), got {decay}")
        self.decay = float(decay)
        self.step = int(step)
        self.shadow: dict[str, torch.Tensor] = {
            name: parameter.detach().clone() for name, parameter in model.named_parameters()
        }

    def __repr__(self) -> str:
        return f"EMA(decay={self.decay:g}, step={self.step}, tensors={len(self.shadow)})"

    def current_decay(self) -> float:
        """The decay in force at the next update: the warmup ramp of the class docstring."""
        return min(self.decay, (1.0 + self.step) / (10.0 + self.step))

    @torch.no_grad()
    def update(self, model: torch.nn.Module) -> None:
        decay = self.current_decay()
        for name, parameter in model.named_parameters():
            self.shadow[name].mul_(decay).add_(parameter.detach(), alpha=1.0 - decay)
        self.step += 1

    def state_dict(self, model: torch.nn.Module) -> dict[str, torch.Tensor]:
        """A full model ``state_dict`` with the averaged parameters and the live buffers.

        Shaped so that ``model.load_state_dict(ema.state_dict(model))`` works: ``policy/export.py``
        writes exactly this into a bundle unless it is asked for the raw weights.
        """
        state = {key: value.detach().clone() for key, value in model.state_dict().items()}
        for name, value in self.shadow.items():
            state[name] = value.detach().clone()
        return state

    def load_state_dict(self, state: dict[str, torch.Tensor], step: int) -> None:
        """Restore the averaged parameters from a checkpoint's ``ema_state_dict`` (resume)."""
        missing = [name for name in self.shadow if name not in state]
        if missing:
            raise KeyError(f"ema_state_dict is missing {len(missing)} parameters, first {missing[0]!r}")
        for name in self.shadow:
            self.shadow[name].copy_(state[name])
        self.step = int(step)
