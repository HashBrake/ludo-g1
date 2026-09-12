"""What both models of CLAUDE.md 5.7 feed on, normalise with and are measured by.

``policy/act.py`` used to import these from ``policy/diffusion.py``, two of them through their
private names (T-030 review); they live here instead, so that neither model is a client of the
other. Nothing in this module knows which architecture it is serving: every function takes the
spec's fields (``goal_channels``, ``cond_state_dim``, ``action_dim``, ``image_hw``) and no spec type.

Four groups, in order:

* **feeding** -- :func:`image_tensor`, :func:`observation_frame`, :func:`with_steps`: one
  :class:`runtime.policy_api.Observation` or one batch turned into the tensors a wrapped lerobot
  policy takes. The Diffusion Policy takes an observation-step dimension and ACT does not; that is
  the only branch, and it is :func:`with_steps`.
* **normalisation** -- :class:`Normalizer` and :func:`dataset_stats`: the statistics lerobot 0.4.4
  keeps in a processor pipeline built around a hub checkpoint, carried instead as buffers in the
  model's own ``state_dict`` so that an exported bundle is self-contained.
* **measurement** -- :func:`benchmark`, :func:`synthetic_observation` and
  :func:`set_torch_threads`: the identical latency measurement for both models, which is what makes
  D-019's comparison a comparison, and the one place the torch thread pool of D-020 is sized.
* **termination** (T-040) -- :class:`DoneHead`, :class:`FeatureTap`, :class:`DoneDetector` and
  :func:`done_settings`: the auxiliary episode-end head of CLAUDE.md 5.5, identical in both models
  so that the termination signal is a property of the data and not of the architecture.

Nothing here moves the robot (R1, R2).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import torch
from torch import Tensor, nn

from engine.interface import Command, Primitive
from policy.dataset import CAMERAS
from runtime import config
from runtime.policy_api import GOAL_CHANNELS, Observation
from runtime.types import ACTION_DIM

__all__ = [
    "BUNDLE_FILE",
    "DONE_KEY",
    "EPS",
    "IMAGE_KEYS",
    "WEIGHTS_FILE",
    "DoneDetector",
    "DoneHead",
    "FeatureTap",
    "Normalizer",
    "benchmark",
    "dataset_stats",
    "done_settings",
    "image_tensor",
    "observation_frame",
    "set_torch_threads",
    "synthetic_observation",
    "with_steps",
]

#: The lerobot feature key of each camera. Anything under ``observation.images.`` is a VISUAL feature
#: to lerobot; the stacked tensor it builds internally is ``observation.images`` (no trailing name).
IMAGE_KEYS: dict[str, str] = {name: f"observation.images.{name}" for name in CAMERAS}
#: Division guard, the value ``NormalizerProcessorStep`` uses.
EPS = 1e-8
#: What an inference bundle holds (see :func:`policy.export.export`).
BUNDLE_FILE = "bundle.json"
WEIGHTS_FILE = "weights.pt"
#: The per-frame episode-end label ``policy/dataset.py`` puts in every sample (T-040), and the key
#: both wrappers look for in a batch before they add the done loss to their own.
DONE_KEY = "done"
#: The thread count :func:`set_torch_threads` put in force in this process, or None before the first
#: call. Process state, because the torch thread pool is process state.
_THREADS: int | None = None


class _Spec(Protocol):
    """The three spec fields the normalisation needs; both policy specs have them."""

    goal_channels: int
    action_dim: int

    @property
    def cond_state_dim(self) -> int: ...


# --------------------------------------------------------------------------------------------------
# feeding: an Observation, or a batch, as the tensors a wrapped lerobot policy takes
# --------------------------------------------------------------------------------------------------


def image_tensor(image: np.ndarray, device: torch.device) -> Tensor:
    """``(h, w, 3)`` uint8 or float to ``(3, h, w)`` float32 in [0, 1], the dataset's convention."""
    array = np.asarray(image)
    tensor = torch.as_tensor(np.ascontiguousarray(array.transpose(2, 0, 1)), device=device)
    return tensor.to(torch.float32) / 255.0 if array.dtype == np.uint8 else tensor.to(torch.float32)


def observation_frame(observation: Observation, device: torch.device) -> dict[str, Tensor]:
    """One observation as one frame of the batch both models take, on ``device``.

    The goal channels are concatenated onto the `top` RGB exactly as ``policy/dataset.py`` does, so a
    frame built here and a frame read from a recorded episode are the same tensor.
    """
    frame = {name: image_tensor(getattr(observation, name), device) for name in CAMERAS}
    goal = torch.as_tensor(np.asarray(observation.goal), dtype=torch.float32, device=device)
    if goal.shape[-2:] != frame["top"].shape[-2:]:
        raise ValueError(f"goal {tuple(goal.shape)} does not match `top` {tuple(frame['top'].shape)}")
    frame["top"] = torch.cat([frame["top"], goal], dim=0)
    frame["state"] = torch.as_tensor(observation.state, dtype=torch.float32, device=device)
    frame["task_id"] = torch.as_tensor(observation.task_id, dtype=torch.float32, device=device)
    return frame


def with_steps(x: Tensor, n_obs_steps: int, *, base_ndim: int) -> Tensor:
    """Give ``x`` an observation-step dimension, repeating the frame if it has none.

    ``base_ndim`` is the rank of a batch of single frames: 4 for ``(B, C, H, W)`` images, 2 for
    ``(B, D)`` vectors. One more than that is a batch that already carries the step dimension.

    Since T-034 ``policy/dataset.py`` yields real history for the cameras and the state, so those
    arrive with the dimension already. What is still repeated here is what is **constant over an
    episode** -- the task one-hot -- where repeating is exact rather than an approximation, and a
    single frame handed in by a caller that has no history (a shape test, a one-frame probe).
    """
    if x.ndim == base_ndim:
        return x.unsqueeze(1).expand(-1, n_obs_steps, *([-1] * (base_ndim - 1)))
    if x.ndim != base_ndim + 1:
        raise ValueError(f"expected a tensor of rank {base_ndim} or {base_ndim + 1}, got shape {tuple(x.shape)}")
    if x.shape[1] != n_obs_steps:
        raise ValueError(f"expected {n_obs_steps} observation steps, got {x.shape[1]}")
    return x


# --------------------------------------------------------------------------------------------------
# normalisation (lerobot 0.4.4 keeps it in a processor pipeline; a bundle has to carry it itself)
# --------------------------------------------------------------------------------------------------


class Normalizer(nn.Module):
    """Dataset statistics as buffers, so they travel in the ``state_dict`` and onto the device.

    VISUAL is mean/std and STATE/ACTION are min/max onto [-1, 1] -- the mapping
    ``DiffusionConfig.normalization_mapping`` declares, with the formulas of
    ``lerobot/processor/normalize_processor.py:325-359``. Until :meth:`load_stats` is called the
    buffers are the identity, which is what an untrained model in a shape test wants.
    """

    def __init__(self, spec: _Spec) -> None:
        super().__init__()
        channels = {name: (3 + spec.goal_channels if name == "top" else 3) for name in CAMERAS}
        for name, count in channels.items():
            self.register_buffer(f"{name}_mean", torch.zeros(count, 1, 1))
            self.register_buffer(f"{name}_std", torch.ones(count, 1, 1))
        for what, size in (("state", spec.cond_state_dim), ("action", spec.action_dim)):
            self.register_buffer(f"{what}_min", -torch.ones(size))
            self.register_buffer(f"{what}_max", torch.ones(size))
        self.register_buffer("fitted", torch.zeros((), dtype=torch.bool))

    def load_stats(self, stats: Mapping[str, Tensor]) -> None:
        """Install statistics from :func:`dataset_stats` (keys ``top_mean`` ... ``action_max``)."""
        own = dict(self.named_buffers())
        for key, value in stats.items():
            if key not in own:
                raise KeyError(f"unknown statistic {key!r}; known: {sorted(k for k in own if k != 'fitted')}")
            if tuple(value.shape) != tuple(own[key].shape):
                raise ValueError(f"statistic {key} has shape {tuple(value.shape)}, expected {tuple(own[key].shape)}")
            own[key].copy_(value.to(own[key].dtype))
        self.fitted.fill_(True)

    def image(self, name: str, x: Tensor) -> Tensor:
        mean, std = getattr(self, f"{name}_mean"), getattr(self, f"{name}_std")
        return (x - mean) / (std + EPS)

    def _min_max(self, what: str, x: Tensor, inverse: bool) -> Tensor:
        low, high = getattr(self, f"{what}_min"), getattr(self, f"{what}_max")
        span = torch.where(high - low == 0, torch.full_like(high, EPS), high - low)
        return (x + 1) / 2 * span + low if inverse else 2 * (x - low) / span - 1

    def state(self, x: Tensor) -> Tensor:
        return self._min_max("state", x, inverse=False)

    def action(self, x: Tensor) -> Tensor:
        return self._min_max("action", x, inverse=False)

    def unnormalize_action(self, x: Tensor) -> Tensor:
        return self._min_max("action", x, inverse=True)


def _frames(image: Tensor) -> Tensor:
    """``(C, H, W)`` or ``(S, C, H, W)`` as ``(S, C, H, W)``: one statistic over the whole history."""
    return image if image.ndim == 4 else image.unsqueeze(0)


def dataset_stats(dataset, samples: int = 256, seed: int = 0) -> dict[str, Tensor]:
    """Per-channel image mean/std and state/action min/max over up to ``samples`` random samples.

    A pass over every frame of a real session is minutes of PNG decoding for numbers that converge in
    hundreds of samples, so this draws a reproducible random subset (``seed``) and says how many in
    the run record. The state statistic covers the 12-D vector the policy sees (state + task one-hot).

    Every observation frame of a sample counts once, so a dataset with ``n_obs_steps`` history and one
    without give the same statistics up to the resampling of the episode's first frames. Each camera
    is divided by **its own** pixel count: `palm` is 320x240 where `top` is 640x480 (T-034; before
    that every camera was divided by `top`'s count, which scaled the `palm` mean by 4).
    """
    count = min(int(samples), len(dataset))
    if count < 1:
        raise ValueError("dataset_stats needs at least one sample")
    order = np.random.default_rng(int(seed)).permutation(len(dataset))[:count]
    sums: dict[str, Tensor] = {}
    squares: dict[str, Tensor] = {}
    pixels: dict[str, int] = dict.fromkeys(CAMERAS, 0)
    lows: dict[str, Tensor] = {}
    highs: dict[str, Tensor] = {}
    for index in order:
        sample = dataset[int(index)]
        for name in CAMERAS:
            image = _frames(sample[name]).to(torch.float64)
            sums[name] = image.sum(dim=(0, 2, 3)) + sums.get(name, 0)
            squares[name] = (image**2).sum(dim=(0, 2, 3)) + squares.get(name, 0)
            pixels[name] += int(image.shape[0] * image.shape[2] * image.shape[3])
        state = sample["state"]
        task = sample["task_id"]
        if state.ndim == 2 and task.ndim == 1:  # history on the state, one task one-hot per episode
            task = task.unsqueeze(0).expand(state.shape[0], -1)
        vectors = {
            "state": torch.cat([state, task], dim=-1).to(torch.float64),
            "action": sample["action"].to(torch.float64),
        }
        for what, value in vectors.items():
            flat = value.reshape(-1, value.shape[-1])
            low, high = flat.min(dim=0).values, flat.max(dim=0).values
            lows[what] = low if what not in lows else torch.minimum(lows[what], low)
            highs[what] = high if what not in highs else torch.maximum(highs[what], high)
    stats: dict[str, Tensor] = {}
    for name in CAMERAS:
        mean = sums[name] / pixels[name]
        variance = torch.clamp(squares[name] / pixels[name] - mean**2, min=0.0)
        stats[f"{name}_mean"] = mean.to(torch.float32).reshape(-1, 1, 1)
        stats[f"{name}_std"] = variance.sqrt().to(torch.float32).reshape(-1, 1, 1)
    for what in ("state", "action"):
        stats[f"{what}_min"] = lows[what].to(torch.float32)
        stats[f"{what}_max"] = highs[what].to(torch.float32)
    return stats


# --------------------------------------------------------------------------------------------------
# measurement (CLAUDE.md 5.8: 10 Hz, or the fallback ladder of D-019)
# --------------------------------------------------------------------------------------------------


def benchmark(adapter: Any, observation: Observation, trials: int = 20, warmup: int = 2) -> dict[str, float]:
    """Mean/p95 wall-clock cost of an adapter's ``act`` over ``trials`` calls.

    ``adapter`` is any :class:`runtime.policy_api.Policy` that records ``last_timing``: both adapters
    of 5.7 do, and the point of one function is that D-019 compares one measurement with itself.
    """
    adapter.reset(Command(Primitive.MOVE, None, None, None))
    for _ in range(max(int(warmup), 0)):
        adapter.act(observation)
    timings: list[dict[str, float]] = []
    for _ in range(int(trials)):
        adapter.act(observation)
        timings.append(dict(adapter.last_timing))
    out = {key: float(np.mean([t[key] for t in timings])) for key in timings[0]}
    out["median_ms"] = float(np.median([t["act_ms"] for t in timings]))
    out["p95_ms"] = float(np.percentile([t["act_ms"] for t in timings], 95))
    out["trials"] = float(trials)
    return out


def synthetic_observation(sizes: Mapping[str, Iterable[int]], task_dim: int, seed: int = 0) -> Observation:
    """An observation of the configured frame sizes, for a latency measurement with no hardware."""
    rng = np.random.default_rng(seed)
    frames = {name: rng.integers(0, 256, (int(hw[1]), int(hw[0]), 3), dtype=np.uint8)
              for name, hw in ((n, list(v)) for n, v in sizes.items())}
    goal = rng.random((GOAL_CHANNELS, frames["top"].shape[0], frames["top"].shape[1])).astype(np.float32)
    task = np.zeros(task_dim, dtype=np.float32)
    task[0] = 1.0
    return Observation(ts_ns=0, top=frames["top"], oblique=frames["oblique"], palm=frames["palm"],
                       state=np.zeros(ACTION_DIM), goal=goal, task_id=task)


def set_torch_threads(config_root: Path | str | None = None, *, threads: int | None = None) -> int:
    """Size torch's intra-op thread pool from ``config/training.yaml`` ``compute.torch_threads``.

    D-020: torch guesses one thread per core (14 here) and oversubscribes the small tensors both
    models of 5.7 are made of -- ACT's ``act()`` went from 3.7 s to 154 ms between the guess and 4
    threads. So the count is a configured value, applied **once per process**: ``policy/train.py``
    and ``eval/run_eval.py`` call this at the start of ``main``, and both adapters call it in their
    constructors, which is where a bundle is first turned into a model.

    Idempotent by design, and that is not a detail: a process that builds two adapters must not
    resize a pool that is already running, and a caller that has deliberately set its own count (a
    DataLoader worker sets 1) must not be overridden by the next adapter it builds. The first call
    in a process wins; every later one returns what is in force and changes nothing. Returns the
    thread count now in force.
    """
    global _THREADS
    if _THREADS is not None:
        return _THREADS
    count = int(config.load("training", root=config_root)["compute"]["torch_threads"]) if threads is None \
        else int(threads)
    if count < 1:
        raise ValueError(f"compute.torch_threads must be >= 1, got {count}")
    torch.set_num_threads(count)
    _THREADS = count
    return count


# --------------------------------------------------------------------------------------------------
# termination (CLAUDE.md 5.5: "the policy's own termination signal or a 20 s timeout"), T-040
# --------------------------------------------------------------------------------------------------


def done_settings(config_root: Path | str | None = None) -> dict[str, float]:
    """``config/training.yaml`` ``done``, validated: the one block both models and the dataset read.

    One block rather than a copy in each policy block, for the same reason ``rates`` and
    ``observation`` are shared: the label is a property of the *recorded episode*, not of an
    architecture, and two models trained on two different windows would not be the comparison of 5.7.
    The values are copied verbatim into each model's spec (and from there into its bundle), so an
    exported policy carries the window it was trained on and the thresholds it stops on.
    """
    block = config.load("training", root=config_root)["done"]
    out = {
        "window_s": float(block["window_s"]),
        "loss_weight": float(block["loss_weight"]),
        "hidden_dim": int(block["hidden_dim"]),
        "threshold": float(block["threshold"]),
        "hold_steps": int(block["hold_steps"]),
    }
    if out["window_s"] < 0:
        raise config.ConfigError(f"config/training.yaml done.window_s must be >= 0, got {out['window_s']}")
    if out["loss_weight"] < 0:
        raise config.ConfigError(f"config/training.yaml done.loss_weight must be >= 0, got {out['loss_weight']}")
    if out["hidden_dim"] < 1:
        raise config.ConfigError(f"config/training.yaml done.hidden_dim must be >= 1, got {out['hidden_dim']}")
    if not 0.0 < out["threshold"] < 1.0:
        raise config.ConfigError(
            f"config/training.yaml done.threshold is a probability in (0, 1), got {out['threshold']}"
        )
    if out["hold_steps"] < 1:
        raise config.ConfigError(f"config/training.yaml done.hold_steps must be >= 1, got {out['hold_steps']}")
    return out


class DoneHead(nn.Module):
    """One logit -- "this primitive is ending" -- from the pooled observation features (5.5).

    A two-layer MLP on exactly the features the action head is conditioned on: the Diffusion
    Policy's global conditioning vector, ACT's pooled transformer-encoder output. Nothing else is
    added to either model, so the termination signal costs one matrix multiply per call and shares
    every encoder with the actions.

    **The output layer starts at zero**, the same convention as the goal projection of both wrappers:
    an untrained head returns a logit of exactly 0, so its probability is 0.5, so
    :class:`DoneDetector` (which needs *above* its threshold) never fires for a model that has not
    learned the signal. A default of "not done" is what the 20 s timeout of 5.5 already covers.
    """

    def __init__(self, in_dim: int, hidden_dim: int) -> None:
        super().__init__()
        if in_dim < 1 or hidden_dim < 1:
            raise ValueError(f"DoneHead needs positive dimensions, got in_dim={in_dim} hidden_dim={hidden_dim}")
        self.in_dim, self.hidden_dim = int(in_dim), int(hidden_dim)
        self.net = nn.Sequential(nn.Linear(self.in_dim, self.hidden_dim), nn.ReLU(), nn.Linear(self.hidden_dim, 1))
        with torch.no_grad():
            self.net[-1].weight.zero_()
            self.net[-1].bias.zero_()

    def __repr__(self) -> str:
        return f"DoneHead(in_dim={self.in_dim}, hidden_dim={self.hidden_dim})"

    def forward(self, features: Tensor) -> Tensor:
        """``(B, in_dim)`` pooled features to ``(B,)`` logits."""
        if features.ndim != 2 or features.shape[1] != self.in_dim:
            raise ValueError(f"DoneHead expects (B, {self.in_dim}) features, got {tuple(features.shape)}")
        return self.net(features).squeeze(-1)

    def loss(self, features: Tensor, labels: Tensor) -> Tensor:
        """BCE-with-logits against the per-frame label of ``policy/dataset.py`` (``done``)."""
        target = labels.to(features.dtype).reshape(-1)
        logits = self(features)
        if target.shape != logits.shape:
            raise ValueError(f"done labels {tuple(target.shape)} do not match the batch {tuple(logits.shape)}")
        return nn.functional.binary_cross_entropy_with_logits(logits, target)

    def probability(self, features: Tensor) -> Tensor:
        return torch.sigmoid(self(features))


class FeatureTap:
    """Read a tensor out of a lerobot module we wrap but never patch (R2, section 7).

    Both models compute the pooled features the done head wants *inside* a lerobot call whose only
    return value is a loss or an action chunk: the Diffusion Policy passes ``global_cond`` into its
    U-Net, ACT's transformer encoder produces the token sequence its decoder attends to. Rather than
    running the vision encoders a second time to get them back -- which would make training and
    inference measurably slower for a head that is a single matrix multiply -- a standard PyTorch
    forward hook on the module *instance we built* catches the tensor as it goes past. Nothing under
    ``third_party/`` or in the installed package is modified, and the hook is registered on an
    instance this process owns.

    The captured tensor is part of the live autograd graph in training, so a gradient from the done
    loss reaches the shared encoders exactly as one from the action loss does.
    """

    def __init__(self) -> None:
        self.value: Tensor | None = None

    def __repr__(self) -> str:
        return f"FeatureTap(captured={None if self.value is None else tuple(self.value.shape)})"

    def clear(self) -> None:
        """Drop the reference, so a captured graph does not outlive the step that made it."""
        self.value = None

    def take(self, what: str) -> Tensor:
        """The captured tensor, cleared as it is handed over. Raises if nothing was captured."""
        if self.value is None:
            raise RuntimeError(
                f"{what} was not captured: the wrapped lerobot module did not run, or its call signature "
                "changed (policy/_shared.py FeatureTap)"
            )
        value, self.value = self.value, None
        return value

    def watch_keyword(self, module: nn.Module, name: str):
        """Capture the keyword argument ``name`` of every call to ``module``."""

        def hook(_module, _args, kwargs):
            value = kwargs.get(name)
            if value is not None:
                self.value = value

        return module.register_forward_pre_hook(hook, with_kwargs=True)

    def watch_output(self, module: nn.Module):
        """Capture the output of every call to ``module``."""

        def hook(_module, _args, output):
            self.value = output

        return module.register_forward_hook(hook)


class DoneDetector:
    """``hold_steps`` consecutive probabilities above ``threshold`` and the primitive is over.

    The state behind both adapters' ``done()``. One probability is folded in per ``act()`` call --
    the same forward pass that produced the actions, never a second one -- and ``done()`` reports
    what the counter says. So the signal the controller reads at tick *n* is the probability computed
    at tick *n - 1*: ``runtime/controller.py`` asks ``done(obs)`` before ``act(obs)``, and one policy
    period (100 ms at ``rates.policy_hz``) is the cost of not running the encoders twice.

    Requiring agreement over ``hold_steps`` calls is why a threshold crossing on one frame -- a
    glimpse of a released horse through a moving hand -- does not end a primitive.
    """

    def __init__(self, threshold: float, hold_steps: int) -> None:
        if not 0.0 < float(threshold) < 1.0:
            raise ValueError(f"DoneDetector threshold is a probability in (0, 1), got {threshold}")
        if int(hold_steps) < 1:
            raise ValueError(f"DoneDetector needs hold_steps >= 1, got {hold_steps}")
        self.threshold = float(threshold)
        self.hold_steps = int(hold_steps)
        self.reset()

    def __repr__(self) -> str:
        return (f"DoneDetector(threshold={self.threshold:g}, hold_steps={self.hold_steps}, "
                f"streak={self.streak}, p={self.probability:.3f})")

    def reset(self) -> None:
        """A new primitive is not a continuation of the last one (``Policy.reset``)."""
        self.streak = 0
        self.probability = 0.0
        self.calls = 0

    def update(self, probability: float) -> bool:
        """Fold in one call's probability and return whether the primitive is now done."""
        self.probability = float(probability)
        self.streak = self.streak + 1 if self.probability > self.threshold else 0
        self.calls += 1
        return self.fired

    @property
    def fired(self) -> bool:
        return self.streak >= self.hold_steps
